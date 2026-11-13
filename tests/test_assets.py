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


def _payload(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", text)
    assert match, f"{path.name} has no embedded PNG data URI"
    _png_from_b64(match.group(1))
    return match.group(1)


def test_logo_data_uri_is_a_valid_png():
    _payload(REPO_ROOT / "frontend" / "src" / "assets" / "logoData.ts")


def test_inline_favicon_is_a_valid_png():
    _payload(REPO_ROOT / "frontend" / "index.html")


def test_favicon_svg_uses_the_exact_logo_payload():
    """Regression: the tab icon used a different eye drawing than the logo."""
    logo = _payload(REPO_ROOT / "frontend" / "src" / "assets" / "logoData.ts")
    favicon = _payload(REPO_ROOT / "frontend" / "public" / "favicon.svg")
    inline = _payload(REPO_ROOT / "frontend" / "index.html")
    assert favicon == logo, "favicon.svg drifted from the logo artwork"
    assert inline == logo, "index.html favicon drifted from the logo artwork"


def test_favicon_ico_wraps_the_logo_png():
    """The .ico must be a valid ICO container holding the same PNG bytes."""
    logo = base64.b64decode(
        _payload(REPO_ROOT / "frontend" / "src" / "assets" / "logoData.ts")
    )
    ico = (REPO_ROOT / "frontend" / "public" / "favicon.ico").read_bytes()

    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind, count) == (0, 1, 1), "not a single-image ICO"
    width, height, _, _, planes, depth, size, offset = struct.unpack(
        "<BBBBHHII", ico[6:22]
    )
    assert (width or 256, height or 256) == (128, 128)
    assert (planes, depth) == (1, 32)
    assert offset == 22 and size == len(logo)
    assert ico[offset:offset + size] == logo, "favicon.ico embeds different artwork"
    assert ico[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
