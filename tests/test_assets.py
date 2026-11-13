"""Static-asset integrity checks.

Branding assets are embedded as base64 data URIs, and a single truncated
character silently degrades the UI to the <img> alt text. These tests keep the
committed payloads decodable (no browser or node toolchain required).
"""
from __future__ import annotations

import base64
import pathlib
import re
import struct
import zlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _png_from_b64(b64: str) -> bytes:
    data = base64.b64decode(b64)
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), "payload is not a PNG"

    pos, idat, seen_iend = 8, b"", False
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        assert pos + 12 + length <= len(data), f"truncated chunk {kind!r}"
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        if kind == b"IEND":
            seen_iend = True
        pos += 12 + length

    assert seen_iend, "PNG is missing IEND"
    width, height = struct.unpack(">II", data[16:24])
    bit_depth, color_type, interlace = data[24], data[25], data[28]
    raw = zlib.decompress(idat)  # raises on a corrupt/truncated image
    if interlace == 0 and bit_depth == 8:
        channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
        if channels:
            assert len(raw) == height * (1 + width * channels)
    return data


def test_logo_data_uri_is_a_valid_png():
    source = (REPO_ROOT / "frontend" / "src" / "assets" / "logoData.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", source)
    assert match, "logoData.ts no longer exports a PNG data URI"
    _png_from_b64(match.group(1))


def test_inline_favicon_is_a_valid_png():
    html = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html)
    assert match, "index.html no longer inlines a PNG favicon"
    _png_from_b64(match.group(1))
