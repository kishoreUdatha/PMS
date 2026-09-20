"""Object storage for uploaded media (MinIO / S3-compatible).

Images are stored in MinIO — never in Postgres — and the database keeps only
the object key. Browsers get a time-limited presigned URL generated at read
time, so the bucket stays private and no credential ever reaches the client.

Two clients exist because the service and the browser reach MinIO at different
addresses inside Docker: uploads go over the internal endpoint, while presigned
URLs must be signed against the address the browser will actually use.
Presigning is a local computation, so the public client never opens a socket.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from functools import lru_cache

import boto3
from botocore.client import Config
# ClientError is the bucket answering with a refusal; BotoCoreError covers
# never reaching it at all -- a stopped MinIO raises EndpointConnectionError,
# which is neither a ClientError nor an OSError and used to escape this
# module as a bare 500 with a stack trace.
from botocore.exceptions import BotoCoreError, ClientError

# Only these are accepted for room and room-type photography.
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB, matching the mockup's stated limit

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_-]+")


class ObjectStoreError(Exception):
    pass


@dataclass(frozen=True)
class ObjectStoreConfig:
    endpoint: str
    public_endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    secure: bool = False
    url_ttl_seconds: int = 7 * 24 * 3600


def _normalise(endpoint: str, secure: bool) -> str:
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    return f"{'https' if secure else 'http'}://{endpoint}"


@lru_cache(maxsize=4)
def _clients(cfg: ObjectStoreConfig):
    """Build (internal, public) S3 clients once per configuration."""
    common = {
        "aws_access_key_id": cfg.access_key,
        "aws_secret_access_key": cfg.secret_key,
        "config": Config(signature_version="s3v4"),
        "region_name": "us-east-1",
    }
    internal = boto3.client(
        "s3", endpoint_url=_normalise(cfg.endpoint, cfg.secure), **common
    )
    public = boto3.client(
        "s3", endpoint_url=_normalise(cfg.public_endpoint, cfg.secure), **common
    )
    return internal, public


def ensure_bucket(cfg: ObjectStoreConfig) -> None:
    """Create the bucket on first use. Safe to call repeatedly."""
    internal, _ = _clients(cfg)
    try:
        internal.head_bucket(Bucket=cfg.bucket)
        return
    except BotoCoreError as exc:
        # Never reached the server at all, which is a different thing from the
        # bucket being absent -- and the one that used to escape this module
        # as a bare 500, because it is neither ClientError nor OSError.
        raise ObjectStoreError(f"Object storage is unreachable: {exc}") from exc
    except ClientError:
        pass                      # no such bucket yet; create it below

    try:
        internal.create_bucket(Bucket=cfg.bucket)
    except BotoCoreError as exc:
        raise ObjectStoreError(f"Object storage is unreachable: {exc}") from exc
    except ClientError as exc:    # or a concurrent caller created it first
        if exc.response.get("Error", {}).get("Code") not in (
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        ):
            raise ObjectStoreError(f"Could not create bucket: {exc}") from exc


def build_key(*parts: str, content_type: str) -> str:
    """Deterministic, collision-free object key: prefix segments + a uuid."""
    suffix = ALLOWED_CONTENT_TYPES[content_type]
    clean = [_SAFE_SEGMENT.sub("-", p).strip("-") for p in parts if p]
    return "/".join([*clean, f"{uuid.uuid4().hex}{suffix}"])


def put_object(cfg: ObjectStoreConfig, key: str, data: bytes, content_type: str) -> str:
    """Upload bytes and return the stored key."""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ObjectStoreError(f"Unsupported image type: {content_type}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ObjectStoreError("Image is larger than 5 MB")
    if not data:
        raise ObjectStoreError("Image is empty")

    ensure_bucket(cfg)
    internal, _ = _clients(cfg)
    try:
        internal.put_object(
            Bucket=cfg.bucket, Key=key, Body=data, ContentType=content_type
        )
    except (BotoCoreError, ClientError) as exc:
        raise ObjectStoreError(f"Upload failed: {exc}") from exc
    return key


#: An export is not an upload from a browser. It has no user behind it, it is
#: written once by the platform itself, and a tenant's whole history does not
#: fit in the 5 MB a profile photo is allowed.
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def put_archive(
    cfg: ObjectStoreConfig, key: str, data: bytes,
    content_type: str = "application/gzip",
) -> str:
    """Upload a generated archive. Same bucket, different rules to put_object.

    Kept separate rather than loosening put_object: the image path's narrow
    content types and small limit are what stop a browser upload becoming an
    arbitrary file write, and that should not be relaxed for everyone because
    one caller inside the platform needs it.
    """
    if not data:
        raise ObjectStoreError("Refusing to store an empty archive")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ObjectStoreError(
            f"Archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")

    ensure_bucket(cfg)
    internal, _ = _clients(cfg)
    try:
        internal.put_object(
            Bucket=cfg.bucket, Key=key, Body=data, ContentType=content_type
        )
    except (BotoCoreError, ClientError) as exc:
        raise ObjectStoreError(f"Archive upload failed: {exc}") from exc
    return key


def get_archive(cfg: ObjectStoreConfig, key: str) -> bytes:
    """Read an archive back. Used to prove one is retrievable, not just sent."""
    internal, _ = _clients(cfg)
    try:
        return internal.get_object(Bucket=cfg.bucket, Key=key)["Body"].read()
    except (BotoCoreError, ClientError) as exc:
        raise ObjectStoreError(f"Archive could not be read back: {exc}") from exc


def delete_object(cfg: ObjectStoreConfig, key: str) -> None:
    internal, _ = _clients(cfg)
    try:
        internal.delete_object(Bucket=cfg.bucket, Key=key)
    except (BotoCoreError, ClientError) as exc:
        raise ObjectStoreError(f"Delete failed: {exc}") from exc


def presigned_url(cfg: ObjectStoreConfig, key: str) -> str:
    """A time-limited GET URL the browser can load directly."""
    _, public = _clients(cfg)
    return public.generate_presigned_url(
        "get_object",
        Params={"Bucket": cfg.bucket, "Key": key},
        ExpiresIn=cfg.url_ttl_seconds,
    )


# --- thumbnails -----------------------------------------------------------
#
# A room photograph arrives at whatever size the camera produced. The booking
# page shows it in a box about 120 pixels wide, so sending the original means
# sending a few megabytes to render a thumbnail -- on a phone, on hotel wifi,
# three times over. Resizing once at upload is the cheap fix; resizing per
# request would put image decoding in the path of every page view.

#: Long edge of a generated thumbnail, in pixels.
#:
#: The booking page draws these at 116x88 CSS pixels, so 640 covers a 2x
#: display with room to spare and still leaves the file small. Larger would be
#: spending bytes nobody can see.
THUMB_MAX_EDGE = 640

#: Thumbnails are always JPEG. A photograph is what this is for, and PNG --
#: which is what phones and screenshots often produce -- stores photographic
#: content several times larger for no visible gain.
THUMB_CONTENT_TYPE = "image/jpeg"
THUMB_QUALITY = 82


def thumbnail_key(key: str) -> str:
    """Where the thumbnail for ``key`` lives.

    Derived rather than random, so the pair can always be found from either
    half -- deleting a photo can delete its thumbnail without a second lookup,
    and a backfill can tell at a glance what is missing.

    Kept under its own prefix rather than beside the original: it makes the
    thumbnails trivially separable, which matters the day the sizing changes
    and every one of them has to be regenerated.
    """
    return f"thumbs/{key.rsplit('.', 1)[0]}.jpg"


def make_thumbnail(data: bytes) -> bytes:
    """Resize an uploaded image down to a thumbnail. Returns JPEG bytes.

    Raises :class:`ObjectStoreError` if the bytes are not a readable image --
    the caller decides whether that is fatal. For an upload it is not: a photo
    that cannot be resized is still a photo, and refusing the whole upload
    because the thumbnail failed would be the wrong trade.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ObjectStoreError("Pillow is not installed") from exc

    import io as _io

    try:
        with Image.open(_io.BytesIO(data)) as img:
            # EXIF orientation, applied before anything else. A photo taken on
            # a phone held sideways is stored sideways with a flag saying so;
            # resizing without honouring it produces a thumbnail on its side
            # while the original looks fine, which reads as a bug in the page.
            img = ImageOps.exif_transpose(img)
            # Flatten transparency onto white. A JPEG has no alpha channel, and
            # the default composite is black -- which turns a logo with a
            # transparent background into a black rectangle.
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, (255, 255, 255))
                flat.paste(img, mask=img.split()[-1])
                img = flat
            elif img.mode != "RGB":
                img = img.convert("RGB")

            img.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), Image.LANCZOS)
            out = _io.BytesIO()
            img.save(out, format="JPEG", quality=THUMB_QUALITY, optimize=True,
                     progressive=True)
            return out.getvalue()
    except ObjectStoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises many things
        raise ObjectStoreError(f"Could not read image: {exc}") from exc


def put_thumbnail(cfg: ObjectStoreConfig, key: str, data: bytes) -> str | None:
    """Generate and store the thumbnail for an already-uploaded object.

    Returns its key, or ``None`` when one could not be made. Failure is
    deliberately not an error: the photo itself is already stored, and the
    only consequence of no thumbnail is a page that loads the original --
    slower, but correct. Losing the upload over it would be worse.
    """
    try:
        small = make_thumbnail(data)
    except ObjectStoreError:
        return None
    tkey = thumbnail_key(key)
    internal, _ = _clients(cfg)
    try:
        internal.put_object(Bucket=cfg.bucket, Key=tkey, Body=small,
                            ContentType=THUMB_CONTENT_TYPE)
    except ClientError:
        return None
    return tkey
