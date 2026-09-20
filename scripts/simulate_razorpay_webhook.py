#!/usr/bin/env python
"""Send a correctly signed Razorpay callback, to test settlement without a card.

Paying by hand tests the guest's half of the flow. It does not test ours, which
is where the interesting failures live: a signature that does not verify, an
order that matches no intent, a retry that credits a folio twice, a booking that
takes the money and never confirms. Those need a callback, and waiting for a
real one means a card, a browser and a person every time.

This forges one properly. The webhook secret is a value *we* chose, so we can
compute the same HMAC Razorpay would and exercise the real path end to end --
signature check, duplicate suppression, folio credit, booking confirmation.

    python scripts/simulate_razorpay_webhook.py <order_id>
    python scripts/simulate_razorpay_webhook.py <order_id> --bad-signature

It reads the callback URL and secret from the deployment itself, so it cannot
drift from what is actually configured.

**Not a substitute for one real payment.** It proves our side handles a callback
correctly; it cannot prove Razorpay is pointed at the right URL, which is the
other way this fails in practice.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def compose_exec(service: str, *args: str) -> str:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", service, *args],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout


def deployment_secret() -> tuple[str, str]:
    """The live callback URL and its secret, read out of the running system.

    Unsealed inside the finance container, because that is the only place the
    encryption key exists -- and reading it there rather than copying it into
    this script is what keeps the secret in one place.
    """
    code = (
        "import json;"
        "from finance_service.database import SessionFactory;"
        "from finance_service.credentials import _unseal;"
        "from sqlalchemy import text;"
        "s=SessionFactory();"
        "r=s.execute(text('SELECT webhook_ref, webhook_secret_sealed FROM "
        "finance.payment_credentials WHERE enabled LIMIT 1')).mappings().first();"
        "print(json.dumps({'ref': r['webhook_ref'],"
        " 'secret': _unseal(r['webhook_secret_sealed'])}) if r else '{}')"
    )
    out = compose_exec("finance", "python", "-c", code).strip().splitlines()[-1]
    data = json.loads(out)
    if not data:
        raise SystemExit("No enabled payment credentials found.")

    base = ""
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("APP_BASE_URL="):
            base = line.split("=", 1)[1].strip().rstrip("/")
    if not base:
        raise SystemExit("APP_BASE_URL is not set in .env")
    return f"{base}/api/finance/webhooks/razorpay/{data['ref']}", data["secret"]


def expected_paise(order_id: str) -> int:
    """What the intent for this order says the guest owes.

    Looked up rather than assumed. A hardcoded figure quietly tests the wrong
    thing: the webhook credits whatever the gateway reports, so a simulation
    that always says the same number posts a partial payment against a more
    expensive booking and reports success while doing it.
    """
    code = (
        "from finance_service.database import SessionFactory;"
        "from sqlalchemy import text;"
        "s=SessionFactory();"
        "v=s.execute(text('SELECT expected_amount FROM finance.payment_intents "
        "WHERE provider_order_id = :o'), {'o': %r}).scalar();"
        "print(int(round(float(v) * 100)) if v is not None else 0)" % order_id
    )
    out = compose_exec("finance", "python", "-c", code).strip().splitlines()[-1]
    amount = int(out)
    if amount <= 0:
        raise SystemExit(f"No payment intent found for order {order_id}.")
    return amount


def event_for(order_id: str, amount_paise: int) -> dict:
    """A payment.captured event shaped like Razorpay's."""
    now = int(time.time())
    return {
        "entity": "event",
        "event": "payment.captured",
        "contains": ["payment"],
        "created_at": now,
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_sim{now}",
                    "entity": "payment",
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "captured",
                    "order_id": order_id,
                    "method": "card",
                    "captured": True,
                }
            }
        },
    }


def post(url: str, body: bytes, signature: str, event_id: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            # Razorpay signs the raw body and puts the digest here. The event
            # id is what makes a retry recognisable as one.
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": event_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    order_id = argv[1]
    bad = "--bad-signature" in argv

    url, secret = deployment_secret()
    amount_paise = expected_paise(order_id)
    print(f"callback: {url}")
    print(f"amount:   {amount_paise / 100:.2f} (from the intent)")

    body = json.dumps(event_for(order_id, amount_paise)).encode()
    # The *raw* body is what gets signed. Re-serialising the parsed JSON would
    # change whitespace and key order, and the signature would stop matching
    # for a reason that looks like an attack and is not.
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    signature = ("0" * 64) if bad else good
    event_id = f"evt_sim{int(time.time())}"

    print(f"signature: {'DELIBERATELY WRONG' if bad else 'valid'}")
    status, text = post(url, body, signature, event_id)
    print(f"-> HTTP {status} {text.strip()[:200]}")

    if bad:
        ok = status == 400
        print("PASS: forged signature refused" if ok
              else f"FAIL: expected 400, got {status}")
        return 0 if ok else 1

    if status != 200:
        print(f"FAIL: expected 200, got {status}")
        return 1

    # Send it again with the same id. Delivery is at least once, so a retry
    # after a timeout is indistinguishable from a new event except by its id --
    # and crediting a folio twice is the failure that costs real money.
    print("\nreplaying the same event id (Razorpay retries on timeout)...")
    status2, text2 = post(url, body, signature, event_id)
    duplicate = '"duplicate"' in text2
    print(f"-> HTTP {status2} {text2.strip()[:200]}")
    print("PASS: retry recognised as a duplicate" if duplicate
          else "FAIL: retry was not suppressed")
    return 0 if duplicate else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
