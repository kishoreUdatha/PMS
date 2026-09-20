#!/usr/bin/env python
"""Point this deployment at a different Channex account, safely.

    python scripts/swap_channex_account.py --key <NEW_KEY> [--url <API_URL>]
    python scripts/swap_channex_account.py --key <NEW_KEY> --check-only

Moving between Channex accounts -- staging to production, or one account to
another -- invalidates every identifier this system has stored. The group ids,
the property ids, the room and rate mappings, the channel ids: all of them
belong to the old account and mean nothing in the new one.

None of that has to be cleared by hand. Provisioning verifies a stored id
before trusting it and rebuilds when it is gone (see ``_is_gone`` and
``_forget_external`` in ``channel_provision``), so the swap is: change the
key, restart, let it run. This script does that in the right order and then
*checks* it worked, because "the sweep will fix it" is a promise worth
verifying the first time it matters.

What it will not do is let the swap happen with a webhook address that cannot
receive a booking. That is the one failure here that loses money rather than
time, and it is the likeliest, because a quick tunnel gets a new hostname
every time it restarts.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

#: Hosts whose name changes on restart. A booking delivered to yesterday's
#: hostname is a booking nobody sees.
EPHEMERAL = ("trycloudflare.com", "ngrok.io", "ngrok-free.app", "loca.lt")


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def write_env(updates: dict[str, str]) -> None:
    text = ENV.read_text(encoding="utf-8")
    for key, value in updates.items():
        pattern = rf"(?m)^{re.escape(key)}=.*$"
        if re.search(pattern, text):
            text = re.sub(pattern, f"{key}={value}", text)
        else:
            text = text.rstrip("\n") + f"\n{key}={value}\n"
    ENV.write_text(text, encoding="utf-8")


def api(url: str, key: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(url.rstrip("/") + path,
                                 headers={"user-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT,
                          capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="the new Channex API key")
    ap.add_argument("--url", help="API base, e.g. https://app.channex.io/api/v1")
    ap.add_argument("--check-only", action="store_true",
                    help="run the checks and change nothing")
    ap.add_argument("--allow-ephemeral-webhook", action="store_true",
                    help="proceed even though the public address is a "
                         "disposable tunnel. Fine for a rehearsal; never for "
                         "an account taking real bookings.")
    args = ap.parse_args()

    env = read_env()
    new_url = args.url or env.get("CHANNEX_API_URL", "")
    base = env.get("APP_BASE_URL", "")

    print("Checks")
    print("------")

    # 1. Does the key work, and against which account?
    status, body = api(new_url, args.key, "/groups")
    if status != 200:
        print(f"  FAIL  the key was refused by {new_url} ({status})")
        print(f"        {body}")
        return 1
    # Parsed, not counted. Counting the word "title" in the body gave the
    # wrong number the first time this ran, and a confident wrong number in a
    # preflight check is worse than no number.
    try:
        groups = len(json.loads(body).get("data") or [])
        where = f"{groups} group(s) on that account"
    except Exception:  # noqa: BLE001
        where = "responded"
    print(f"  ok    key accepted by {new_url} ({where})")

    # 2. Is the address a booking can actually be delivered to?
    if not base:
        print("  FAIL  APP_BASE_URL is empty, so no webhook can be registered")
        return 1
    if not base.startswith("https://"):
        print(f"  FAIL  APP_BASE_URL is not https ({base})")
        return 1
    ephemeral = any(h in base for h in EPHEMERAL)
    if ephemeral:
        line = ("WARN " if args.allow_ephemeral_webhook else "FAIL ")
        print(f"  {line} APP_BASE_URL is a disposable tunnel:")
        print(f"        {base}")
        print("        Its hostname changes every restart. The webhook "
              "registered")
        print("        at Channex would then point at a host that no longer "
              "exists,")
        print("        and arriving bookings would be delivered nowhere.")
        print("        Use a real domain, or pass "
              "--allow-ephemeral-webhook for a rehearsal.")
        if not args.allow_ephemeral_webhook:
            return 1
    else:
        print(f"  ok    APP_BASE_URL is a stable address ({base})")

    # 3. Is that address actually reachable from outside?
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/health",
                                    timeout=20) as r:
            reachable = r.status
    except Exception as e:  # noqa: BLE001
        reachable = f"unreachable ({e})"
    print(f"  {'ok   ' if reachable == 200 else 'WARN '} {base}/health -> "
          f"{reachable}")

    if not env.get("CHANNEX_WEBHOOK_SECRET"):
        print("  FAIL  CHANNEX_WEBHOOK_SECRET is empty, so the webhook "
              "endpoint")
        print("        refuses everything rather than trusting unsigned posts")
        return 1
    print("  ok    webhook secret is set")

    if args.check_only:
        print("\nChecks only; nothing changed.")
        return 0

    # --- the swap ---------------------------------------------------------
    print("\nSwapping")
    print("--------")
    write_env({"CHANNEX_API_KEY": args.key,
               **({"CHANNEX_API_URL": args.url} if args.url else {})})
    print("  .env updated")

    r = compose("up", "-d", "--build", "booking-core")
    if r.returncode != 0:
        print("  FAIL  booking-core did not come up")
        print(r.stderr[-800:])
        return 1
    print("  booking-core rebuilt and restarted")

    # Provision every property that wants channel distribution. The stored
    # ids all belong to the old account, so each one is found missing and
    # rebuilt -- which is the whole point of doing this with a script rather
    # than by hand.
    print("\nRebuilding at the new account")
    print("-----------------------------")
    r = compose("exec", "-T", "booking-core", "python", "-c", PROVISION)
    print(r.stdout.rstrip() or r.stderr[-1200:])
    return 0 if r.returncode == 0 else 1


PROVISION = """
import time
from booking_core.database import SessionFactory
from booking_core import channel_provision as cp

# A moment for the service to finish starting and migrations to settle.
time.sleep(6)
results = cp.provision_all(SessionFactory, retry_minutes=0, limit=50)
if not results:
    print("  nothing to provision: no property has an OTA connection yet")
for r in results:
    print(f"  {r.property_id}  {r.status}"
          f"  rooms {r.rooms_mapped}  rates {r.rates_mapped}"
          f"  channels {r.channels_created}")
    for p in r.problems:
        print(f"      - {p}")
"""


if __name__ == "__main__":
    sys.exit(main())
