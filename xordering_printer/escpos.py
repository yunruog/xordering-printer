"""ESC/POS bytes for a ticket: text mode (Chinese as GBK) or image mode (a raster picture).

Text mode works on printers with a Chinese (GB18030/GBK) font, which most receipt printers sold
for Chinese-speaking markets have. Image mode draws the ticket with a Chinese font from this
computer (Microsoft YaHei / SimHei / SimSun on Windows) and sends it as a picture (GS v 0), which
every ESC/POS printer can print. Both end with a feed and a cut (GS V 66).
"""
import os
import sys
from functools import lru_cache
from typing import List, Optional

from .ticket import Line, columns, layout, width as text_width

ESC, GS, FS = b"\x1b", b"\x1d", b"\x1c"
INIT = ESC + b"@"
CHINESE_ON = FS + b"&"  # Chinese (GBK double-byte) character mode
CUT = GS + b"V" + bytes([66, 0])  # feed to the cutter and cut (partial cut where supported)


def _size(n: int) -> bytes:
    big = n >= 2
    return GS + b"!" + bytes([0x11 if big else 0x00]) + FS + b"!" + bytes([0x0C if big else 0x00])


def _align(a: str) -> bytes:
    return ESC + b"a" + bytes([1 if a == "center" else 2 if a == "right" else 0])


def _encode(text: str) -> bytes:
    return text.encode("gbk", errors="replace")


def text_bytes(lines: List[Line], paper_width: int = 80) -> bytes:
    out = bytearray(INIT + CHINESE_ON)
    for line in lines:
        if line.kind == "rule":
            out += _size(1) + _align("left") + ESC + b"E\x00" + _encode("-" * columns(paper_width)) + b"\n"
            continue
        if line.kind == "feed":
            out += b"\n"
            continue
        out += _size(line.size) + ESC + b"E" + bytes([1 if line.bold else 0]) + GS + b"B" + bytes([1 if line.invert else 0])
        # Centering is done by layout() with spaces (the same in both modes); the printer aligns left.
        out += _align("left")
        for row in layout(line, paper_width):
            out += _encode(row) + b"\n"
        out += GS + b"B\x00"
    out += _size(1) + ESC + b"E\x00" + ESC + b"d\x03" + CUT
    return bytes(out)


# --- Image mode -------------------------------------------------------------------------------

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]


def find_font() -> Optional[str]:
    windir = os.environ.get("WINDIR")
    candidates = list(FONT_CANDIDATES)
    if windir and sys.platform == "win32":
        candidates = [c.replace(r"C:\Windows", windir) for c in candidates]
    return next((c for c in candidates if os.path.exists(c)), None)


@lru_cache(maxsize=8)
def _font(px: int):
    from PIL import ImageFont

    path = find_font()
    if path:
        return ImageFont.truetype(path, px)
    return ImageFont.load_default()


def render_image(lines: List[Line], paper_width: int = 80):
    """The ticket as a 1-bit picture, as wide as the printable area (576 or 384 dots).

    Uses the same layout as text mode (columns of 12 dots, Chinese two columns wide), but draws
    each run of words with the font's own spacing: prices end at the right edge, centered lines stay
    centered.
    """
    import re

    from PIL import Image, ImageDraw

    dots = 384 if int(paper_width) == 58 else 576
    col = dots / columns(paper_width)  # 12 dots per column, as Font A
    rows = []  # (text, size, bold, invert, align, is_rule)
    for line in lines:
        if line.kind == "rule":
            rows.append(("", 1, False, False, "left", True))
        elif line.kind == "feed":
            rows.append(("", 1, False, False, "left", False))
        else:
            for r in layout(line, paper_width):
                rows.append((r, line.size, line.bold, line.invert, line.align, False))
    heights = [int(col * 2 * size * 1.3) for (_, size, _, _, _, _) in rows]
    img = Image.new("L", (dots, sum(heights) + 24), 255)
    draw = ImageDraw.Draw(img)
    y = 0
    for (text, size, bold, invert, align, rule), h in zip(rows, heights):
        if rule:
            draw.line([(0, y + h // 2), (dots, y + h // 2)], fill=0, width=2)
        elif text.strip():
            font = _font(int(col * 2 * size))
            top, bottom = font.getbbox("国Ag")[1], font.getbbox("国Ag")[3]
            ty = y + (h - (bottom - top)) / 2 - top
            fill = 255 if invert else 0
            if invert:
                draw.rectangle([0, y, dots, y + h - 1], fill=0)
            cols = columns(paper_width, size)
            unit = col * size
            runs = [(m.start(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", text)]
            if align == "center":
                runs = [(len(text) - len(text.lstrip()), text.strip())]
            for m_start, run in runs:
                start = text_width(text[:m_start])
                end = start + text_width(run)
                run_w = draw.textlength(run, font=font)
                if align == "center":
                    x = (dots - run_w) / 2
                elif end >= cols and start > 0:
                    x = dots - run_w  # right-aligned (prices)
                else:
                    x = start * unit
                draw.text((x, ty), run, font=font, fill=fill)
                if bold:
                    draw.text((x + 1, ty), run, font=font, fill=fill)
        y += h
    return img.point(lambda v: 0 if v < 160 else 255, mode="1")


def raster_bytes(img) -> bytes:
    """GS v 0: the picture in bands of at most 128 rows (some printers limit one command's size)."""
    width, height = img.size
    xbytes = (width + 7) // 8
    # Mode "1" packs 8 pixels per byte, MSB first, 1 = white; the printer wants 1 = black.
    packed = bytes(b ^ 0xFF for b in img.convert("1").tobytes())
    if width % 8:
        # Padding bits at the end of each row came out black: clear them.
        mask = (0xFF << (8 - width % 8)) & 0xFF
        rows = [bytearray(packed[y * xbytes:(y + 1) * xbytes]) for y in range(height)]
        for r in rows:
            r[-1] &= mask
        packed = b"".join(bytes(r) for r in rows)
    out = bytearray()
    for top in range(0, height, 128):
        band = min(128, height - top)
        out += GS + b"v0" + bytes([0, xbytes & 0xFF, xbytes >> 8, band & 0xFF, band >> 8])
        out += packed[top * xbytes:(top + band) * xbytes]
    return bytes(out)


def image_bytes(lines: List[Line], paper_width: int = 80) -> bytes:
    return INIT + _align("left") + raster_bytes(render_image(lines, paper_width)) + ESC + b"d\x03" + CUT


def ticket_bytes(lines: List[Line], printer: dict) -> bytes:
    paper = int(printer.get("paperWidth") or 80)
    if printer.get("render") == "image":
        return image_bytes(lines, paper)
    return text_bytes(lines, paper)
