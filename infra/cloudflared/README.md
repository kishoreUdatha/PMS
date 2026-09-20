# Named tunnel: a stable public address for the gateway

A payment gateway has to be able to reach this deployment. Razorpay POSTs a
signed callback to whatever URL each tenant saved in their own dashboard, and
that URL has to resolve from the public internet — a LAN address or `localhost`
receives nothing, and the symptom is a guest who paid and a booking that stays
held.

The quick tunnel in `docker-compose.yml` (`--profile tunnel`) solves that in one
command, but its hostname changes on every restart. That is fine for an
afternoon of testing and wrong for anything a tenant has pasted into Razorpay.
This is the version whose hostname does not change.

## You do not need a paid plan

Creating a tunnel through the **Zero Trust dashboard** requires onboarding to a
Zero Trust plan, and that flow asks for a card even on the free tier. The CLI
below never touches Zero Trust. All it needs is a domain already on your
Cloudflare account — the free DNS plan is enough.

## The four commands

Run these in your own terminal, not through the container: the first one opens
a browser.

```sh
# 1. Authorise this machine and pick your domain in the browser.
#    Writes ~/.cloudflared/cert.pem.
cloudflared tunnel login

# 2. Create the tunnel. Prints a UUID and writes ~/.cloudflared/<UUID>.json
cloudflared tunnel create chirala-pms

# 3. Point a hostname at it. This creates the DNS record for you.
cloudflared tunnel route dns chirala-pms pms.yourdomain.com

# 4. Copy the credentials next to the config, where the container looks.
cp ~/.cloudflared/<UUID>.json infra/cloudflared/
```

Then edit `config.yml` in this directory and replace the three placeholders:
the UUID from step 2 (twice) and the hostname from step 3.

## Running it

```sh
docker compose --profile named-tunnel up -d cloudflared-named
docker compose logs -f cloudflared-named        # expect "Registered tunnel connection"
```

Then point the application at it, so generated webhook URLs use the new
hostname, and restart finance to pick it up:

```sh
# in .env
APP_BASE_URL=https://pms.yourdomain.com

docker compose up -d finance
```

Verify before handing the URL to anyone:

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://pms.yourdomain.com/health
# 200 — the tunnel reaches the gateway

curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://pms.yourdomain.com/api/finance/webhooks/razorpay/probe
# 404 — reached finance, which correctly rejected an unknown callback ref
```

The second check is the one that matters. A 200 on `/health` only proves the
gateway answers; the 404 proves a real signed callback would land on the handler
that settles a payment.

## What is secret here

`config.yml` is committed — it names a hostname and a UUID, neither of which is
a credential. The `<UUID>.json` beside it **is** one: it alone is enough to run
this tunnel, so `.gitignore` keeps `infra/cloudflared/*.json` out of version
control. `~/.cloudflared/cert.pem` is broader still — it can create and delete
tunnels on your account — and should never leave the machine that ran `login`.

## What this exposes

The tunnel publishes the **whole gateway**, not just the webhook path: login,
IAM, every API route. That is the same surface the gateway already presents on
the LAN, but reachable from anywhere and indexed by nothing. It is appropriate
for a staging host and worth thinking about twice on a developer laptop. Stop it
with `docker compose stop cloudflared-named` when it is not needed.
