"""ID scans: short-lived links, bounded reads, and files that are what they say.

No object store is needed: presigning is computed locally, and uploads are
fed to the reader directly.
"""

from __future__ import annotations

import asyncio
import io
import re

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from booking_core import checkin_routes as c

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
WEBP = b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 64
PDF = b"%PDF-1.7\n" + b"\0" * 64


class _Reads(io.BytesIO):
    largest = 0

    def read(self, n=-1):
        out = super().read(n)
        _Reads.largest = max(_Reads.largest, len(out))
        return out


def _upload(data: bytes, ctype: str, size: int | None = None) -> UploadFile:
    return UploadFile(file=_Reads(data), size=size,
                      headers=Headers({"content-type": ctype}))


def _read(data, ctype, size=None):
    return asyncio.run(c._read_doc(_upload(data, ctype, size)))


def test_guest_document_links_expire_in_minutes():
    url = c._doc_url("guest-docs/g/id_front/x.jpg")
    ttl = int(re.search(r"X-Amz-Expires=(\d+)", url).group(1))
    assert ttl == c.settings.guest_document_url_ttl_seconds <= 15 * 60


@pytest.mark.parametrize("data,ctype", [
    (JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp"),
    (PDF, "application/pdf")])
def test_genuine_files_are_accepted(data, ctype):
    assert _read(data, ctype) == (data, ctype)


@pytest.mark.parametrize("data,ctype", [
    (b"<html><script>alert(1)</script></html>", "image/png"),
    (JPEG, "application/pdf"),
    (PDF, "image/jpeg"),
    (b"RIFF\0\0\0\0AVI LIST", "image/webp")])
def test_a_file_that_is_not_its_declared_type_is_refused(data, ctype):
    with pytest.raises(HTTPException) as e:
        _read(data, ctype)
    assert e.value.status_code == 415


def test_oversize_is_refused_without_reading_it_all():
    _Reads.largest = 0
    big = JPEG + b"\0" * (c.MAX_DOC_BYTES + 10)
    with pytest.raises(HTTPException) as e:
        _read(big, "image/jpeg", size=len(big))
    assert e.value.status_code == 413 and _Reads.largest == 0
    # No declared size: read stops one byte past the limit.
    with pytest.raises(HTTPException) as e:
        _read(big, "image/jpeg")
    assert e.value.status_code == 413
    assert _Reads.largest == c.MAX_DOC_BYTES + 1
