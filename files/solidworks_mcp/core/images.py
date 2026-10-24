"""BMP to PNG with the standard library only, so the server needs no image package."""

import struct
import zlib
from typing import Optional, Tuple


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)


def bmp_size(header: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) from the first 26 bytes of a BMP file, or None."""
    if header[:2] == b"BM" and len(header) >= 26:
        width, height = struct.unpack_from("<ii", header, 18)
        if width > 0 and height != 0:
            return width, abs(height)
    return None


def bmp_to_png(bmp: bytes, pad_to: Optional[Tuple[int, int]] = None,
               crop: Optional[Tuple[int, int, int, int]] = None) -> Tuple[bytes, int, int]:
    """
    An uncompressed 24- or 32-bit BMP as an 8-bit RGB PNG. With crop (left, top, width, height in pixels,
    from the top left) only that part is kept. With pad_to the image is centred in a PNG of that size and the
    border takes the colour of its top left pixel. Returns (png bytes, width, height).
    """
    if bmp[:2] != b"BM":
        raise ValueError("Not a BMP file")
    data_offset = struct.unpack_from("<I", bmp, 10)[0]
    width, height = struct.unpack_from("<ii", bmp, 18)
    bpp = struct.unpack_from("<H", bmp, 28)[0]
    compression = struct.unpack_from("<I", bmp, 30)[0]
    if bpp not in (24, 32) or compression not in (0, 3):
        raise ValueError(f"Unsupported BMP format ({bpp}-bit, compression {compression})")

    top_down = height < 0
    height = abs(height)
    step = bpp // 8
    row_size = (width * step + 3) & ~3          # rows are padded to 4 bytes
    rows = []
    for y in range(height):
        start = data_offset + (y if top_down else height - 1 - y) * row_size
        row = bmp[start:start + width * step]
        rgb = bytearray(width * 3)
        rgb[0::3] = row[2::step]                # a BMP stores B, G, R
        rgb[1::3] = row[1::step]
        rgb[2::3] = row[0::step]
        rows.append(rgb)

    if crop:
        left, top = max(0, min(width - 1, crop[0])), max(0, min(height - 1, crop[1]))
        cw, ch = max(1, min(width - left, crop[2])), max(1, min(height - top, crop[3]))
        rows = [r[left * 3:(left + cw) * 3] for r in rows[top:top + ch]]
        width, height = cw, ch

    if pad_to and (pad_to[0] > width or pad_to[1] > height) and rows:
        out_w, out_h = max(pad_to[0], width), max(pad_to[1], height)
        fill = bytes(rows[0][0:3])
        left = (out_w - width) // 2
        top = (out_h - height) // 2
        blank = bytearray(fill * out_w)
        rows = ([blank] * top
                + [bytearray(fill * left) + r + bytearray(fill * (out_w - width - left)) for r in rows]
                + [blank] * (out_h - height - top))
        width, height = out_w, out_h

    raw = bytearray()
    for rgb in rows:
        raw.append(0)                           # PNG filter type 0
        raw += rgb
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + _chunk(b"IEND", b""))
    return png, width, height
