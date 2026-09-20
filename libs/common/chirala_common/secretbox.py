"""Storing a secret we have to be able to read back.

Passwords are hashed and never recovered — see ``passwords.py``. This is the
other kind: a tenant's payment gateway API secret, which we must present to
Razorpay on every call, so it has to survive in a readable form. Hashing is not
an option and plaintext is not acceptable, which leaves symmetric encryption.

**Fernet, from ``cryptography``.** Authenticated (AES-CBC with an HMAC, so a
tampered ciphertext is rejected rather than decrypted to rubbish), versioned,
and hard to hold wrongly — there is no mode to choose and no IV to supply. The
alternative was ``pgcrypto``, which is installed; it lost because the key would
have to travel inside SQL statements, which puts it one ``log_statement = all``
away from the query log.

The encryption happens here, in the service, so the database stores ciphertext
it cannot read and never receives the key.

**Rotation is built in.** ``CREDENTIAL_ENCRYPTION_KEYS`` is a list. The first
key encrypts; every key can decrypt. So rotating is: generate a key, put it at
the front, leave the old one behind until everything has been re-encrypted, then
drop it. A design with one key means rotation requires downtime, and rotation
that requires downtime does not happen.

**No key, no storage.** With nothing configured :func:`seal` raises, exactly as
the mailer refuses to pretend it sent something. A deployment that stored
gateway secrets in plaintext because a variable was unset would be a worse
outcome than one that refused to store them at all.
"""

from __future__ import annotations


class SecretsNotConfigured(RuntimeError):
    """No encryption key is set, so nothing can be sealed or opened."""


def generate_key() -> str:
    """A new key, printable. For ``openssl``-style setup instructions."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _box(keys: str):
    """A MultiFernet over the configured keys, or raise.

    Keys are comma-separated; blanks and whitespace are tolerated because these
    arrive from environment variables that people edit by hand.
    """
    from cryptography.fernet import Fernet, MultiFernet

    parts = [k.strip() for k in (keys or "").split(",") if k.strip()]
    if not parts:
        raise SecretsNotConfigured(
            "No credential encryption key is configured, so payment "
            "credentials cannot be stored. Set CREDENTIAL_ENCRYPTION_KEYS on "
            "the service (generate one with: python -c \"from "
            "chirala_common.secretbox import generate_key; print(generate_key())\")."
        )
    try:
        return MultiFernet([Fernet(p.encode()) for p in parts])
    except (ValueError, TypeError) as exc:
        # A malformed key is a deployment mistake, and saying so plainly beats
        # an InvalidToken three layers down at the first decrypt.
        raise SecretsNotConfigured(
            f"CREDENTIAL_ENCRYPTION_KEYS is not a valid Fernet key list: {exc}"
        ) from None


def seal(keys: str, plaintext: str) -> str:
    """Encrypt with the *first* configured key. Returns text safe to store."""
    if plaintext is None:
        raise ValueError("Nothing to seal.")
    return _box(keys).encrypt(plaintext.encode("utf-8")).decode("ascii")


def open_sealed(keys: str, ciphertext: str) -> str:
    """Decrypt with whichever configured key made it.

    Raises ``SecretsNotConfigured`` when no key is set, and
    ``cryptography.fernet.InvalidToken`` when none of them fits — which means
    either the ciphertext was tampered with or the key that wrote it is gone.
    Both are worth failing loudly for: silently treating an unreadable gateway
    secret as absent would quietly take a tenant's payments offline.
    """
    return _box(keys).decrypt(ciphertext.encode("ascii")).decode("utf-8")


def rotate(keys: str, ciphertext: str) -> str:
    """Re-encrypt an existing value under the current first key."""
    return _box(keys).rotate(ciphertext.encode("ascii")).decode("ascii")
