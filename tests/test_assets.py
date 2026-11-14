"""Static asset integrity checks for EmberEye's single-source brand mark."""
from __future__ import annotations

import pathlib
import struct
import zlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_info(data: bytes) -> tuple[int, int, int, int]:
    assert data.startswith(PNG_SIGNATURE), "payload is not a PNG"
    pos = 8
    idat = bytearray()
    seen_iend = False
    width = height = bit_depth = color_type = interlace = -1
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        end = pos + 8 + length
        assert end + 4 <= len(data), f"truncated chunk {kind!r}"
        payload = data[pos + 8 : end]
        expected_crc = struct.unpack(">I", data[end : end + 4])[0]
        assert zlib.crc32(kind + payload) & 0xFFFFFFFF == expected_crc, f"bad CRC in {kind!r}"
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
            interlace = payload[12]
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            seen_iend = True
            pos = end + 4
            break
        pos = end + 4

    assert seen_iend and pos == len(data), "PNG is incomplete or has trailing bytes"
    raw = zlib.decompress(bytes(idat))
    assert bit_depth == 8 and interlace == 0, "expected a non-interlaced 8-bit PNG"
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    assert channels is not None, f"unsupported PNG color type {color_type}"
    assert len(raw) == height * (1 + width * channels), "incorrect decompressed image size"
    return width, height, bit_depth, color_type


def test_app_brand_mark_is_a_valid_transparent_png():
    logo_path = REPO_ROOT / "frontend" / "public" / "embereye-mark.png"
    width, height, _, color_type = _png_info(logo_path.read_bytes())
    assert (width, height) == (512, 512)
    assert color_type == 6, "logo must retain its RGBA transparency"


def test_app_and_documentation_use_the_shared_mark_path():
    index = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    logo_module = (REPO_ROOT / "frontend" / "src" / "assets" / "logo.ts").read_text(encoding="utf-8")
    assert "/embereye-mark.png" in index
    assert 'EMBEREYE_LOGO_SRC = "/embereye-mark.png"' in logo_module


def test_favicon_ico_contains_a_valid_png():
    ico = (REPO_ROOT / "frontend" / "public" / "favicon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind, count) == (0, 1, 1), "not a single-image ICO"
    width, height, _, _, planes, depth, size, offset = struct.unpack("<BBBBHHII", ico[6:22])
    assert (width or 256, height or 256) == (128, 128)
    assert (planes, depth) == (1, 32)
    assert offset == 22 and size == len(ico) - offset
    png_width, png_height, _, color_type = _png_info(ico[offset:])
    assert (png_width, png_height, color_type) == (128, 128, 6)
